"""Beat count derivation.

Apple Loops never store tempo: Logic derives it as beat_count * 60 / duration.
So a beat count that is off by one makes the loop play at the wrong tempo,
and the whole correctness of "follows my project" rests on this number.
"""

import pytest

from convert_to_apple_loops import beats_per_bar, snap_beat_count


class TestSnapBeatCount:
    def test_snaps_a_trailing_decay_down_to_a_whole_bar(self):
        # 4 bars at 128 BPM is 7.50s; a 0.12s reverb tail reads as 16.26 beats.
        assert snap_beat_count(16.256, beats_per_bar=4) == 16

    def test_snaps_a_slightly_short_loop_up_to_a_whole_bar(self):
        assert snap_beat_count(15.7, beats_per_bar=4) == 16

    def test_leaves_an_exact_musical_length_alone(self):
        assert snap_beat_count(8.0, beats_per_bar=4) == 8

    def test_does_not_snap_a_loop_with_a_full_bar_of_tail(self):
        # 4 bars of music plus a bar of tail is genuinely 20 beats. Snapping it
        # to 16 would make Logic play the loop 25% fast.
        assert snap_beat_count(20.0, beats_per_bar=4) == 20

    def test_refuses_to_snap_a_two_beat_discrepancy(self):
        # 30 beats is 2 short of 8 bars, but 2 beats is half a bar -- far more
        # than a decay tail. Calling it 32 would make Logic play the file 6.7%
        # slow, which is worse than an odd bar count that plays at the right
        # speed. Only sub-beat slop gets absorbed.
        assert snap_beat_count(30.0, beats_per_bar=4) == 30

    def test_absorbs_up_to_three_quarters_of_a_beat_of_tail(self):
        assert snap_beat_count(16.75, beats_per_bar=4) == 16

    def test_honours_a_three_four_bar(self):
        assert snap_beat_count(11.8, beats_per_bar=3) == 12

    def test_a_fragment_shorter_than_one_beat_is_zero_beats(self):
        assert snap_beat_count(0.4, beats_per_bar=4) == 0


class TestBeatsPerBar:
    def test_reads_the_numerator_of_a_time_signature(self):
        assert beats_per_bar("3/4") == 3

    def test_defaults_to_four_for_junk(self):
        assert beats_per_bar("") == 4
        assert beats_per_bar("nonsense") == 4
