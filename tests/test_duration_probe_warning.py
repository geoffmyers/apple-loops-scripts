"""Reporting files whose duration cannot be measured.

`afinfo` fails two different ways: it can be absent (an exception), or it can
run and exit non-zero (no exception, macOS's actual behaviour on an unreadable
file). The second used to return None in silence -- and since the converter no
longer invents a duration, that meant a file quietly converting with no beat
markers and no explanation.

Both paths must report, and a systemic failure must report once rather than
once per file across a few thousand samples.
"""

from pathlib import Path

from convert_to_apple_loops import AppleLoopConverter


class TestDurationFailures:
    def test_one_warning_for_many_files_failing_the_same_way(self, tmp_path, capsys):
        converter = AppleLoopConverter()
        for name in ("a.wav", "b.wav", "c.wav"):
            converter.get_audio_duration(tmp_path / name)

        warnings = [line for line in capsys.readouterr().err.splitlines()
                    if 'duration' in line.lower()]
        assert len(warnings) == 1, warnings

    def test_every_failing_file_is_still_recorded(self, tmp_path):
        converter = AppleLoopConverter()
        for name in ("a.wav", "b.wav", "c.wav"):
            converter.get_audio_duration(tmp_path / name)

        assert len(converter.duration_failures) == 3

    def test_a_file_that_cannot_be_measured_returns_none(self, tmp_path):
        converter = AppleLoopConverter()
        assert converter.get_audio_duration(tmp_path / "missing.wav") is None
