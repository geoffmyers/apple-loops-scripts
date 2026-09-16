"""Where a file's duration comes from.

Duration is half of the tempo Logic recovers (beat_count * 60 / duration), so
a wrong duration is a wrong tempo. The old code fell back to a hardcoded 4.0
seconds when afinfo failed, which turned a 7.5s loop into an 8-beat loop
playing at half speed -- silently, with a full set of beat markers.
"""

from pathlib import Path

from convert_to_apple_loops import AppleLoopConverter, LoopMetadata


class TestResolveDuration:
    def test_the_real_file_wins(self):
        converter = AppleLoopConverter(duration_probe=lambda p: 7.5)
        meta = LoopMetadata(duration=99.0)
        assert converter.resolve_duration(Path("x.wav"), meta) == 7.5

    def test_falls_back_to_the_duration_splice_recorded(self):
        converter = AppleLoopConverter(duration_probe=lambda p: None)
        meta = LoopMetadata(duration=7.5)
        assert converter.resolve_duration(Path("x.wav"), meta) == 7.5

    def test_reports_nothing_rather_than_inventing_a_length(self):
        converter = AppleLoopConverter(duration_probe=lambda p: None)
        assert converter.resolve_duration(Path("x.wav"), LoopMetadata()) is None

    def test_an_unknown_duration_produces_no_beat_count(self):
        # No beat count means no beat markers, which means Logic treats the
        # file as a plain sample. Wrong-but-confident markers are worse.
        converter = AppleLoopConverter(duration_probe=lambda p: None)
        meta = LoopMetadata(tempo=128)
        meta.duration = converter.resolve_duration(Path("x.wav"), meta)
        converter.finalize_beat_count(meta)
        assert meta.beat_count == 0
        assert not converter.wants_beat_markers(meta)
