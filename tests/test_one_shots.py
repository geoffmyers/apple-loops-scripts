"""One-shots must never carry beat markers.

A beat-marker chunk tells Logic the file is a rhythmic phrase it may
time-stretch. Put one on a kick drum and Logic stretches the kick to fit the
project tempo. A Splice library is mostly one-shots, so this is the single
most damaging thing a bulk conversion can get wrong.
"""

from convert_to_apple_loops import (
    BEAT_MARKERS_UUID,
    AppleLoopConverter,
    LoopMetadata,
    MIDIInfo,
)


class TestOneShotMetadata:
    def setup_method(self):
        self.converter = AppleLoopConverter()

    def test_a_loop_declares_its_beat_count(self):
        meta = LoopMetadata(beat_count=16, tempo=128)
        assert b'beat count\x00' in self.converter.create_uuid_chunk(meta)

    def test_a_one_shot_omits_beat_count_even_if_one_was_computed(self):
        meta = LoopMetadata(beat_count=16, tempo=128, is_one_shot=True)
        assert b'beat count\x00' not in self.converter.create_uuid_chunk(meta)

    def test_a_one_shot_keeps_its_key_so_it_is_still_browsable(self):
        meta = LoopMetadata(key_signature='F#', key_type='minor', is_one_shot=True)
        chunk = self.converter.create_uuid_chunk(meta)
        assert b'key signature\x00F#\x00' in chunk
        assert b'key type\x00minor\x00' in chunk

    def test_a_one_shot_is_expressed_by_absence_not_by_a_new_key(self):
        # Logic decides "loop" from the presence of beat markers and a beat
        # count. There is no documented one-shot flag in the metadata chunk,
        # so the state is expressed by omission -- nothing is invented here.
        meta = LoopMetadata(beat_count=16, is_one_shot=True)
        chunk = self.converter.create_uuid_chunk(meta)
        assert b'category\x00' in chunk
        assert b'beat count\x00' not in chunk


class TestBeatMarkerDecision:
    def setup_method(self):
        self.converter = AppleLoopConverter()

    def test_a_loop_with_beats_gets_markers(self):
        assert self.converter.wants_beat_markers(LoopMetadata(beat_count=16))

    def test_a_one_shot_never_gets_markers(self):
        meta = LoopMetadata(beat_count=16, is_one_shot=True)
        assert not self.converter.wants_beat_markers(meta)

    def test_a_file_with_no_beat_count_gets_no_markers(self):
        assert not self.converter.wants_beat_markers(LoopMetadata(beat_count=0))


class TestMidiCafBytes:
    """create_midi_caf is pure Python, so the emitted file can be checked."""

    def setup_method(self):
        self.converter = AppleLoopConverter()
        # A minimal but real SMF: header chunk plus one empty track.
        self.midi = MIDIInfo(
            tempo=120,
            duration=8.0,
            raw_data=(
                b'MThd' + b'\x00\x00\x00\x06' + b'\x00\x00\x00\x01\x01\xe0'
                + b'MTrk' + b'\x00\x00\x00\x04' + b'\x00\xff\x2f\x00'
            ),
        )

    def test_a_midi_loop_carries_the_beat_markers_chunk(self):
        meta = LoopMetadata(beat_count=16, duration=8.0)
        assert BEAT_MARKERS_UUID in self.converter.create_midi_caf(self.midi, meta)

    def test_a_midi_one_shot_carries_no_beat_markers_chunk(self):
        meta = LoopMetadata(beat_count=16, duration=8.0, is_one_shot=True)
        assert BEAT_MARKERS_UUID not in self.converter.create_midi_caf(self.midi, meta)
