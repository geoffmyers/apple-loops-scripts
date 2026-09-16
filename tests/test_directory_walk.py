"""What the directory walk should and should not pick up.

A real Splice library contains directories whose names end in .wav -- an
artefact of how packs are laid out. Globbing for "*.wav" matches them, and they
were being handed to afconvert as if they were audio.
"""

from pathlib import Path

from convert_to_apple_loops import AppleLoopConverter


class TestDirectoryWalkSkipsNonFiles:
    def test_a_directory_named_like_audio_is_not_converted(self, tmp_path):
        library = tmp_path / "lib"
        (library / "NW_LASD_80_drum_passengers.wav").mkdir(parents=True)
        (library / "real.mid").write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
                                           b"MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")

        converter = AppleLoopConverter(output_dir=tmp_path / "out")
        stats = converter.convert_directory(library, use_table=False)

        assert stats["total"] == 1, "the directory should not have been counted"
        assert stats["errors"] == 0


class TestOutputDirectoryIsNeverAnInput:
    """`convert ~/Splice --output-dir ~/Splice/converted` must terminate.

    .caf is a supported input format, so an output directory nested inside the
    input directory means every run re-converts its own output. Observed on a
    real library: 6 files, then 12, then 24.
    """

    def test_files_already_in_the_output_directory_are_skipped(self, tmp_path):
        library = tmp_path / "lib"
        library.mkdir()
        (library / "real.mid").write_bytes(
            b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0"
            b"MTrk\x00\x00\x00\x04\x00\xff\x2f\x00")

        output = library / "converted"
        output.mkdir()
        (output / "already_done.caf").write_bytes(b"caff\x00\x01\x00\x00")

        converter = AppleLoopConverter(output_dir=output)
        stats = converter.convert_directory(library, use_table=False)

        assert stats["total"] == 1, "the previously converted .caf was picked up"
